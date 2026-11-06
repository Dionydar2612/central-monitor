"""
agent_service.py - เครื่อง Windows ที่ส่งข้อมูลเองผ่าน agent (Fluent Bit)
=========================================================================

puller.yaml
    - name: win-app-01
      type: agent            # ไม่ใช้ SSH / puller ข้ามเครื่องนี้
      os: windows
      host: 10.0.0.21        # ไม่บังคับ (แสดงในหน้าเว็บ)
      agent:
        logs: ['C:\\inetpub\\logs\\LogFiles\\*\\*.log']
        channels: [System, Application]

ข้อมูลเข้ามาทาง central-agent-gw (start_push_log/otel/agent-gateway.yaml)
    log      -> OpenSearch (ชื่อไฟล์ agent__<เครื่อง>__<source>__<file> -> logs_service รู้ว่าเป็นของเครื่องไหน)
    สถานะ    -> Prometheus: fluentbit_uptime{cm_web="<เครื่อง>"} ทุก 30 วินาที

สถานะของเครื่อง agent
    Last seen = เวลาล่าสุดที่ได้สัญญาณจาก agent (Prometheus) หรือ log ล่าสุด (ถ้า Prometheus ไม่ได้รับ remote write)
    online    = Last seen ไม่เกิน ONLINE_WITHIN วินาที
"""

import io
import os
import time
import zipfile
from datetime import datetime, timezone

from clients import prom_value
from settings import load_web_targets

ONLINE_WITHIN = 180                    # วินาที (agent ส่งทุก 30 วินาที)
AGENT_DIR = os.getenv("CENTRAL_AGENT_DIR", "/app/agent")
AGENT_CA = os.getenv("CENTRAL_AGENT_CA", "/run/agent-tls/cert.pem")
AGENT_PORT = os.getenv("CENTRAL_AGENT_PORT", "4318")
_HB = '{__name__=~"fluentbit_uptime.*",cm_web="%s"}'


def is_agent(t):
    return isinstance(t, dict) and str(t.get("type") or "ssh") == "agent"


def agent_target(web):
    """target ของเครื่อง agent ชื่อ web หรือ None"""
    return next((t for t in load_web_targets() if t.get("name") == web and is_agent(t)), None)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def last_seen(web):
    """unix เวลาล่าสุดที่ได้ข้อมูลจาก agent หรือ None"""
    hb = _HB % web
    seen = prom_value(f"max(timestamp({hb}))")                       # ภายใน 5 นาทีล่าสุด
    if seen is None:
        seen = prom_value(f"max(max_over_time(timestamp({hb})[1d:1m]))")
    try:
        import logs_service                 # import ช้า (กัน import วน)
        entries = logs_service._scan(web)
        if entries:
            seen = max(seen or 0, entries[0]["_ts"]) or None
    except Exception as e:
        print(f"[AGENT] last log {web}: {e!r}")
    return seen


def status(web):
    """{online, last_seen (ISO), age (วินาที)}"""
    seen = last_seen(web)
    age = time.time() - seen if seen else None
    return {"online": bool(age is not None and age <= ONLINE_WITHIN), "last_seen": _iso(seen), "age": age}


# ---------------------------------------------------------------------------
# ชุดติดตั้ง (หน้า Settings)
# ---------------------------------------------------------------------------

def token():
    return os.getenv("CENTRAL_AGENT_TOKEN", "")


def setup_info(target, central):
    """คำสั่งติดตั้งสำหรับเครื่องนี้  central = IP/ชื่อของ central ที่เครื่อง Windows ใช้เรียก"""
    agent = target.get("agent") or {}
    logs = ";".join(agent.get("logs") or [])
    channels = ",".join(agent.get("channels") or ["System", "Application"])
    tok = token()
    parts = [
        "powershell -ExecutionPolicy Bypass -File .\\install-fluentbit.ps1",
        f"-Central {central or '<IP-ของ-central>'}",
        f"-Port {AGENT_PORT}" if str(AGENT_PORT) != "4318" else "",
        f"-Token {tok or '<CENTRAL_AGENT_TOKEN>'}",
        f"-Name {target.get('name')}",
        f'-Logs "{logs}"' if logs else "",
        f'-Channels "{channels}"',
    ]
    return {
        "name": target.get("name"),
        "command": " ".join(p for p in parts if p),
        "token_set": bool(tok),
        "port": AGENT_PORT,
        "ca_available": os.path.isfile(AGENT_CA),
        "package_available": os.path.isfile(os.path.join(AGENT_DIR, "windows", "install-fluentbit.ps1")),
    }


def package_zip():
    """zip ชุดติดตั้ง: สคริปต์ + README + central-ca.pem (ไม่มี token ในไฟล์)"""
    src = os.path.join(AGENT_DIR, "windows")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(src)):
            path = os.path.join(src, name)
            if os.path.isfile(path):
                z.write(path, name)          # ไม่ซ้อนโฟลเดอร์ (Extract All สร้างโฟลเดอร์ให้เอง)
        if os.path.isfile(AGENT_CA):
            z.write(AGENT_CA, "central-ca.pem")
    return buf.getvalue()
