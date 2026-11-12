"""
api.py - จุดเริ่มต้นของ web_dashboard (Flask): เส้นทาง (routes) ทั้งหมด
=====================================================================

ไฟล์นี้ทำหน้าที่ "รับ request -> เรียกบริการ -> ส่ง JSON กลับ" เท่านั้น
logic จริงแยกอยู่ในโมดูลของตัวเอง:

    settings.py         ค่าตั้งต้น/environment/รายชื่อเครื่องจาก puller.yaml
    auth.py             ตรวจรหัสผ่านตอนล็อกอิน
    clients.py          เรียก Prometheus / OpenSearch
    metrics_service.py  CPU/Memory/Disk (ตอนนี้, กราฟ, ณ เวลาของ log)
    logs_service.py     ค้น/นับ log + ผูกกับ metrics
    system_state.py     อ่านไฟล์สถานะที่ puller เขียน (service/process/event/Last pull)

รันด้วย gunicorn:  gunicorn --bind 0.0.0.0:8000 api:app   (service central-api)
หน้าเว็บ (frontend/) ถูกเสิร์ฟจากไฟล์นี้ที่ "/" และ /dashboard.js, /dashboard.css

ตาราง API (ทุกเส้นต้องล็อกอินก่อน ยกเว้น /health /login /api/login)
    GET  /health                  ตรวจว่าเว็บทำงาน (healthcheck ของ docker)
    GET  /login, /                หน้า login / หน้า dashboard
    POST /api/login | /api/logout | GET /api/me
    GET  /api/web-status          รายชื่อเครื่อง + online/offline (จาก Prometheus `up`)
    GET  /api/overview            ภาพรวมทุกเครื่อง: Total/Up/Down + CPU/Memory/Disk/สถานะ ต่อเครื่อง
    GET  /api/live-processes?web= process ต่อ CPU core + top memory วัดสดผ่าน puller (cache 20 วินาที)
    GET  /api/summary?web=        สรุปเครื่อง: สถานะ, Last pull, จำนวน problem, metrics
    GET  /api/metrics?web=        metrics ตอนนี้ + service/process/event
    GET  /api/metrics/history     กราฟย้อนหลัง (web, start, end เป็น unix วินาที)
    GET  /api/problems?web=&size=&level=&type=   รายการ log ของเครื่อง (level = ERROR,WARN,INFO / type = ชนิด log)
    GET  /api/log-stats?web=      จำนวน log แยกตาม level และ type (ตัวเลขบนปุ่มตัวกรอง)
    GET  /api/log-markers?web=&start=&end=   จุด ERROR/WARN บนเส้นเวลาของกราฟ
         (/api/problems และ /api/log-stats รับ start/end ได้ด้วย = เฉพาะช่วงนั้น)
    GET  /api/problems/detail     log 1 รายการ + สถานะเครื่อง ณ เวลานั้น (web, id)
    GET  /api/logs/search?q=&web=&level=&type=&start=&end=   ค้นข้อความใน log ทั้ง index (web ว่าง = ทุกเครื่อง)
    PUT  /api/targets/<name>/pause   {"paused": true|false}  (operator ขึ้นไป)
    POST /api/logs/ack {"ids","note","web"}  /api/logs/unack {"ids"}   รับทราบ log -> ซ่อน (operator ขึ้นไป)
         /api/problems?acked=1 = รวม log ที่รับทราบแล้ว
    GET  /api/checks              web / SSL checks + ผลล่าสุด   POST/PUT/DELETE /api/checks[/<name>] (admin)
    PUT  /api/checks/<name>/pause    (operator ขึ้นไป)
    GET/POST /api/users  PUT/DELETE /api/users/<name>   จัดการผู้ใช้ (admin)
    GET  /events                  หน้า Status & Logs (sensor ตามสถานะ + System log + Activity log)
    GET  /api/sensors?status=warning,error&web=&history=1   sensor ต่อเครื่อง (puller/sensor_monitor.py)
    GET  /api/events?kind=system|activity&limit=&since=&q=   System log / Activity log (activity = admin)
    GET  /api/events/summary?since_system=&since_activity=   ตัวเลขบนป้ายหัวหน้าเว็บ
    POST /api/sensors/ack {"id","note"}  /api/sensors/unack {"id"}   รับทราบปัญหา (operator ขึ้นไป)
    GET  /api/sensors/history?id=&range=1h|6h|24h|7d|30d               กราฟย้อนหลังของ sensor
    GET  /api/incidents?hours=24&web=   ปัญหาที่เกิดแล้ว (เริ่ม/จบ/นานเท่าไหร่/ค่าสูงสุด) + ที่ยังเป็นอยู่
    ทุก POST/PUT/DELETE + ล็อกอิน/ออก ถูกบันทึกใน Activity log (audit_service.py) โดยอัตโนมัติ

สิทธิ์ (role) ตรวจที่ require_authentication -> _required_role
    viewer   ดูอย่างเดียว      operator  + pause/resume, Load INFO log, เลือก service ที่แสดง
    admin    + Settings ทั้งหมด (เครื่อง, checks, ผู้ใช้)
    GET  /api/info-logs?web=&lines=&source=&start=&end=
                                  ปุ่ม "Load INFO log": INFO สดจากเครื่อง (ผ่าน puller) ไม่เก็บไว้ที่ไหน
         (/api/problems, /api/log-stats แสดงเฉพาะ ERROR / WARN: ระบบไม่เก็บ INFO)

    หน้า Settings (เพิ่ม/แก้/ลบเครื่องใน puller.yaml)  logic อยู่ที่ targets_service.py
    GET  /settings                หน้า Settings (settings.html)
    GET  /api/targets             รายการเครื่อง + public key ของ central
    GET/PUT /api/targets/<name>/services   service ที่เลือกให้แสดงในการ์ด Service
    POST /api/targets             เพิ่มเครื่อง
    PUT  /api/targets/<name>      แก้เครื่อง (ชื่อเปลี่ยนไม่ได้)
    DELETE /api/targets/<name>    ลบเครื่อง
    POST /api/targets/host-key    สแกน SSH host key ของ host:port (ยังไม่เก็บ)
"""

import re
import time

from ruamel.yaml import YAMLError
from flask import Flask, g, jsonify, redirect, request, send_from_directory, session

import settings
from auth import AUTH_USERNAME, check_credentials, session_role
import audit_service
import log_ack_service
import checks_service
import sensors_service
import users_service
import retention_service
import agent_service
import apps_service
import alert_settings_service
import notify_service
import alert_rules_service
import host_details
from users_service import UserError, role_at_least
from clients import prom_value, puller_get
from settings import resolve_web_name
from logs_service import (
    get_errors_15m,
    get_info_logs,
    get_log_detail,
    get_log_markers,
    get_log_stats,
    get_logs,
    get_problem_count,
    search_logs,
)
from metrics_service import (
    classify_state,
    get_current_metrics,
    HISTORY_SETS,
    get_cpu_cores,
    get_metrics_history,
    get_web_prometheus_config,
    _labels,
)
from overview_service import get_overview
import overview_history
from settings import STATIC_DIR, load_web_targets
from system_state import last_pull_time
import targets_service
from targets_service import ValidationError

# static_url_path="" : ไฟล์ใน frontend/ เสิร์ฟที่ราก เช่น /dashboard.js
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")

app.secret_key = settings.SECRET_KEY

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,       # JS อ่าน cookie ไม่ได้
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=settings.COOKIE_SECURE,   # true เมื่อใช้ HTTPS
)

# ช่วงเวลาสูงสุดที่ขอกราฟย้อนหลังได้ (31 วัน) กันการ query หนักเกินไป
MAX_HISTORY_SECONDS = 31 * 24 * 3600


# ============================================================
# ป้องกันทุกเส้นทาง: ต้องล็อกอินก่อน
# ============================================================

@app.before_request
def require_authentication():
    """ทำงานก่อนทุก request เส้นทางที่เปิดให้ไม่ต้องล็อกอิน: /health /login /api/login /static/
    ที่เหลือ: /api/* ตอบ 401, หน้าเว็บ redirect ไป /login"""
    if request.path in ("/health", "/login", "/api/login"):
        return None

    if request.path.startswith("/static/"):
        return None

    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication_required"}), 401
        return redirect("/login")

    # role ปัจจุบัน (ผู้ใช้ถูกลบ -> ออกจากระบบทันที, ถูกลดสิทธิ์ -> มีผลทันที)
    role = session_role(session.get("username", ""), session.get("role"))
    if role is None:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication_required"}), 401
        return redirect("/login")
    g.role = role

    needed = _required_role(request.method, request.path)
    if not role_at_least(role, needed):
        if request.path.startswith("/api/"):
            return jsonify({"error": "forbidden", "required_role": needed}), 403
        return redirect("/")
    return None


_PAUSE_PATH = re.compile(r"^/api/(targets|checks)/[^/]+/pause$|^/api/(sensors|logs)/(un)?ack$")
_SERVICES_PATH = re.compile(r"^/api/targets/[^/]+/services$")
_TARGET_PATH = re.compile(r"^/api/targets/(?!host-key$|agent-package\.zip$)[^/]+$")


def _required_role(method, path):
    """role ขั้นต่ำของแต่ละเส้นทาง (ไม่ระบุ = viewer)"""
    if _PAUSE_PATH.match(path):
        return "operator"
    if _SERVICES_PATH.match(path):
        return "viewer" if method == "GET" else "operator"
    if path in ("/settings", "/settings.html") or path.startswith("/api/alert-rules"):
        return "operator"                          # operator: Settings = เครื่อง (แท็บการแจ้งเตือน + Log) + Custom alert rules
    if path == "/api/targets" and method == "GET":
        return "operator"
    if _TARGET_PATH.match(path) and method == "PUT":
        return "operator"                          # operator แก้ได้เฉพาะการแจ้งเตือน + log (targets_update ตรวจอีกชั้น)
    if path == "/api/alert-settings" and method == "GET":
        return "operator"                          # ค่าเริ่มต้นสำหรับแสดงในช่องว่าง
    if path.startswith(("/api/targets", "/api/users", "/api/retention", "/api/alert-settings", "/api/notify-settings")):
        return "admin"
    if path.startswith(("/api/checks", "/api/apps")):
        return "viewer" if method == "GET" else "admin"
    if path == "/api/info-logs":
        return "operator"
    if path == "/api/events" and request.args.get("kind") == "activity":
        return "admin"
    return "viewer"


# ============================================================
# Activity log: บันทึกทุกการเปลี่ยนแปลงอัตโนมัติ (ไม่เก็บรหัสผ่าน)
# ============================================================

_AUDIT_RULES = [
    # (method, regex ของ path, ชื่อการกระทำ, ฟิลด์ที่เก็บจาก body)
    ("POST", re.compile(r"^/api/targets$"), "target.add", ("name", "host", "group", "paused")),
    ("PUT", re.compile(r"^/api/targets/(?P<t>[^/]+)/pause$"), "target.pause", ("paused",)),
    ("PUT", re.compile(r"^/api/targets/(?P<t>[^/]+)/services$"), "target.services", ()),
    ("POST", re.compile(r"^/api/targets/(?P<t>[^/]+)/deploy-key$"), "target.deploy_key", ("admin_user",)),
    ("POST", re.compile(r"^/api/targets/host-key$"), "target.scan_host_key", ("host", "port")),
    ("PUT", re.compile(r"^/api/targets/(?P<t>[^/]+)$"), "target.update", ("host", "group", "paused")),
    ("DELETE", re.compile(r"^/api/targets/(?P<t>[^/]+)$"), "target.delete", ()),
    ("POST", re.compile(r"^/api/checks$"), "check.add", ("name", "url", "group")),
    ("PUT", re.compile(r"^/api/checks/(?P<t>[^/]+)/pause$"), "check.pause", ("paused",)),
    ("PUT", re.compile(r"^/api/checks/(?P<t>[^/]+)$"), "check.update", ("url", "group", "paused")),
    ("DELETE", re.compile(r"^/api/checks/(?P<t>[^/]+)$"), "check.delete", ()),
    ("POST", re.compile(r"^/api/sensors/ack$"), "sensor.ack", ("id", "note")),
    ("POST", re.compile(r"^/api/sensors/unack$"), "sensor.unack", ("id",)),
    ("POST", re.compile(r"^/api/logs/ack$"), "log.ack", ("web", "note")),
    ("POST", re.compile(r"^/api/logs/unack$"), "log.unack", ("web",)),
    ("POST", re.compile(r"^/api/users$"), "user.add", ("username", "role")),
    ("PUT", re.compile(r"^/api/retention$"), "settings.retention",
     ("log_retention", "incident_retention", "system_history_retention")),
    ("PUT", re.compile(r"^/api/alert-settings$"), "settings.alerts",
     ("alert_minutes", "hysteresis", "bottleneck", "critical_services", "thresholds", "thresholds_windows")),
    ("PUT", re.compile(r"^/api/notify-settings$"), "settings.notify", ("enabled", "levels", "summary_times", "popup")),
    ("POST", re.compile(r"^/api/notify-settings/test$"), "settings.notify_test", ()),
    ("PUT", re.compile(r"^/api/alert-rules$"), "settings.alert_rules", ()),
    ("POST", re.compile(r"^/api/apps$"), "apps.add", ("type", "web", "value", "name")),
    ("DELETE", re.compile(r"^/api/apps/(?P<t>[^/]+)$"), "apps.delete", ()),
    ("PUT", re.compile(r"^/api/apps/(?P<t>[^/]+)$"), "apps.update", ("type", "web", "value", "name")),
    ("POST", re.compile(r"^/api/apps/bulk$"), "apps.bulk_add", ("web", "ports")),
    ("POST", re.compile(r"^/api/alert-rules/test$"), "settings.alert_rules_test", ("name",)),
    ("POST", re.compile(r"^/api/notify-settings/summary-now$"), "settings.notify_summary_now", ()),
    ("PUT", re.compile(r"^/api/users/(?P<t>[^/]+)$"), "user.update", ("role",)),
    ("DELETE", re.compile(r"^/api/users/(?P<t>[^/]+)$"), "user.delete", ()),
]


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else "") or request.remote_addr or ""


def audit(action, target=None, ok=True, status=None, details=None, user=None, role=None):
    audit_service.append_audit({
        "user": user if user is not None else session.get("username", ""),
        "role": role if role is not None else (getattr(g, "role", None) or session.get("role")),
        "ip": _client_ip(), "action": action, "target": target, "ok": ok,
        "status": status, "details": details or {},
    })


@app.after_request
def audit_changes(response):
    if request.method not in ("POST", "PUT", "DELETE") or not request.path.startswith("/api/"):
        return response
    for method, rx, action, fields in _AUDIT_RULES:
        m = rx.match(request.path)
        if method != request.method or not m:
            continue
        body = request.get_json(silent=True) or {}
        details = {k: body.get(k) for k in fields if isinstance(body, dict) and k in body}
        if action == "target.services" and isinstance(body, dict):
            details["count"] = len(body.get("services") or [])
        if action in ("log.ack", "log.unack") and isinstance(body, dict):
            details["count"] = len(body.get("ids") or [])
        if action == "user.update" and isinstance(body, dict) and body.get("password"):
            details["password_changed"] = True
        target = m.groupdict().get("t") or (body.get("name") or body.get("username") or body.get("host") or body.get("id")
                                           if isinstance(body, dict) else None)
        audit(action, target, ok=response.status_code < 400, status=response.status_code, details=details)
        break
    return response


# ============================================================
# หน้าเว็บ + ล็อกอิน
# ============================================================

@app.get("/health")
def health():
    """healthcheck ของ docker (ไม่ต้องล็อกอิน)"""
    return jsonify({"status": "ok"})


@app.get("/")
def index():
    """หน้า dashboard (frontend/index.html)"""
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/login")
def login_page():
    """หน้า login (frontend/login.html) ถ้าล็อกอินอยู่แล้วส่งกลับหน้าแรก"""
    if session.get("authenticated"):
        return redirect("/")

    return send_from_directory(STATIC_DIR, "login.html")


@app.post("/api/login")
def login():
    """รับ {"username","password"} ถูกต้อง -> ตั้ง session"""
    data = request.get_json(silent=True) or {}

    role = check_credentials(data.get("username", ""), data.get("password", ""))
    if not role:
        audit("login", data.get("username", ""), ok=False, status=401, user=str(data.get("username", ""))[:64], role="")
        return jsonify({"error": "invalid_credentials"}), 401

    session.clear()
    session["authenticated"] = True
    session["username"] = data.get("username", "")
    session["role"] = role
    audit("login", session["username"], ok=True, status=200, role=role)

    return jsonify({"status": "ok"})


@app.post("/api/logout")
def logout():
    if session.get("authenticated"):
        audit("logout", session.get("username", ""), ok=True, status=200)
    session.clear()
    return jsonify({"status": "ok"})


@app.get("/api/me")
def current_user():
    """ใครล็อกอินอยู่ (หน้าเว็บโชว์ "Logged in as ...")"""
    if not session.get("authenticated"):
        return jsonify({"authenticated": False}), 401

    role = getattr(g, "role", None) or session.get("role") or "viewer"
    return jsonify({"authenticated": True, "username": session.get("username", ""), "role": role})


# ============================================================
# ผู้ใช้ (admin)
# ============================================================

def _user_error(e):
    return jsonify({"error": "validation", "fields": e.fields}), e.status


@app.get("/api/targets/<name>/agent-setup")
def agent_setup(name):
    """คำสั่งติดตั้ง agent ของเครื่อง Windows นี้ (admin)  central = IP/ชื่อของ central ที่เครื่อง Windows ใช้เรียก"""
    t = agent_service.agent_target(name)
    if not t:
        return jsonify({"error": "ไม่พบเครื่อง agent ชื่อนี้"}), 404
    central = (request.args.get("central") or request.host.rsplit(":", 1)[0]).strip("[]")
    if not re.match(r"^[A-Za-z0-9.:-]{1,253}$", central):
        return jsonify({"error": "central ต้องเป็น IP หรือชื่อเครื่อง"}), 400
    return jsonify({**agent_service.setup_info(t, central), "central": central,
                    "status": agent_service.status(name)})


@app.get("/api/targets/agent-package.zip")
def agent_package():
    """ชุดติดตั้ง agent Windows (สคริปต์ + central-ca.pem) ไม่มี token ในไฟล์"""
    try:
        data = agent_service.package_zip()
    except OSError as e:
        return jsonify({"error": f"ไม่พบชุดติดตั้ง: {e}"}), 500
    return app.response_class(data, mimetype="application/zip", headers={
        "Content-Disposition": "attachment; filename=cm-agent-windows.zip", "Cache-Control": "no-store"})


@app.get("/api/retention")
def retention_get():
    """อายุการเก็บข้อมูล: log (OpenSearch ISM), ประวัติปัญหา, สถานะเครื่องย้อนหลัง, metrics (อ่านอย่างเดียว)"""
    return jsonify(retention_service.get_settings())


@app.put("/api/retention")
def retention_put():
    ok, out = retention_service.update_settings(request.get_json(silent=True) or {}, session.get("username", ""))
    if not ok:
        return jsonify(out), 400
    return jsonify({**out, "settings": retention_service.get_settings()})


@app.get("/api/users")
def users_list():
    return jsonify({"users": users_service.list_users(), "builtin_admin": AUTH_USERNAME,
                    "roles": list(users_service.ROLES)})


@app.post("/api/users")
def users_add():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        return jsonify(users_service.add_user(body, AUTH_USERNAME)), 201
    except UserError as e:
        return _user_error(e)


@app.put("/api/users/<name>")
def users_update(name):
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    if name == session.get("username") and body.get("role") not in (None, "admin"):
        return jsonify({"error": "validation", "fields": {"role": "ลดสิทธิ์ตัวเองไม่ได้"}}), 400
    try:
        user = users_service.update_user(name, {k: body[k] for k in ("role", "password") if k in body})
    except UserError as e:
        return _user_error(e)
    if user is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(user)


@app.delete("/api/users/<name>")
def users_delete(name):
    if name == session.get("username"):
        return jsonify({"error": "ลบบัญชีที่กำลังใช้อยู่ไม่ได้"}), 400
    if not users_service.delete_user(name):
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "ok"})


# ============================================================
# เครื่อง + metrics
# ============================================================

@app.get("/api/web-status")
def web_status():
    """รายชื่อเครื่องทั้งหมดใน puller.yaml + สถานะ online/offline

    online = Prometheus เห็น up{web="<ชื่อ>"} == 1
    label "web" มาจาก data/prometheus/web-nodes.json ที่ puller สร้าง
    รายชื่อตรงกับหน้า Settings เสมอ (ไม่มีเครื่อง central: เครื่องกลางไม่ใช่ target)
    ยังไม่มีเครื่องเลย -> คืน [] แล้วหน้าเว็บแสดงว่ายังไม่มีเครื่อง
    """
    result = []

    for target in load_web_targets():
        web = target.get("name")

        if not web:
            continue

        value = prom_value(f'up{{web="{web}"}}')

        result.append({"web": web, "status": "online" if value == 1 else "offline"})

    return jsonify(result)


@app.get("/api/alert-settings")
def alert_settings_get():
    """เกณฑ์แจ้งเตือนทั้งระบบ (Linux / Windows), ระยะเวลาก่อนแจ้งเตือน, service สำคัญ (admin)"""
    try:
        return jsonify(alert_settings_service.get_settings())
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.put("/api/alert-settings")
def alert_settings_put():
    try:
        ok, out = alert_settings_service.update_settings(request.get_json(silent=True) or {})
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (200 if ok else 400)


@app.get("/api/popup-settings")
def popup_settings_get():
    """ป๊อปอัปแจ้งเตือนบนหน้าเว็บ: เปิดไหม + เรื่องที่แจ้ง (เหมือน Teams) ทุก role อ่านได้"""
    try:
        return jsonify(notify_service.popup_settings())
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.get("/api/notify-settings")
def notify_settings_get():
    """การแจ้งเตือน Microsoft Teams (admin) ไม่ส่ง webhook URL เต็มกลับไป"""
    try:
        return jsonify(notify_service.get_settings())
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.put("/api/notify-settings")
def notify_settings_put():
    try:
        ok, out = notify_service.update_settings(request.get_json(silent=True) or {})
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (200 if ok else 400)


@app.post("/api/notify-settings/test")
def notify_settings_test():
    """ส่งการ์ดทดสอบไปที่ Teams  body: {webhook_url?} (ไม่ใส่ = ใช้ที่บันทึกไว้)"""
    body = request.get_json(silent=True) or {}
    try:
        ok, msg = notify_service.send_test(body.get("webhook_url") or None)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify({"ok": ok, "message": msg, **({} if ok else {"error": msg})}), (200 if ok else 502)


@app.get("/apps")
def apps_page():
    """หน้า Apps & Web (frontend/apps.html)"""
    return send_from_directory(STATIC_DIR, "apps.html")


@app.get("/api/apps")
def apps_list():
    """โปรแกรม / เว็บ / พอร์ต ที่เฝ้าดู + สถานะ"""
    try:
        return jsonify(apps_service.list_apps(request.args.get("web") or None, with_stats=request.args.get("stats") != "0"))
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.post("/api/apps")
def apps_add():
    try:
        body = _json_body()
        if body is None:
            return jsonify({"error": "ต้องส่งเป็น JSON"}), 400
        ok, out = apps_service.add_app(body)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (201 if ok else 400)


@app.get("/api/apps/<rule_id>/detail")
def apps_detail(rule_id):
    """หน้ารายละเอียด Monitor: สถานะ + กราฟเวลาตอบ / ขึ้น-ลง + uptime + ประวัติปัญหา  ?web=&range=1h|24h|7d|30d"""
    try:
        d = apps_service.detail(rule_id, request.args.get("web") or None, request.args.get("range") or "24h")
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if d is None:
        return jsonify({"error": "ไม่พบรายการนี้"}), 404
    return jsonify(d)


@app.post("/api/apps/bulk")
def apps_bulk():
    """เฝ้าดูหลายเว็บบนเครื่องเดียว  body: {web, ports: [...]}"""
    body = _json_body()
    if body is None:
        return jsonify({"error": "ต้องส่งเป็น JSON"}), 400
    try:
        ok, out = apps_service.bulk_add(body)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (201 if ok else 400)


@app.get("/api/apps/suggest")
def apps_suggest():
    """ชื่อที่เลือกได้ในฟอร์มเพิ่ม (โปรแกรม / พอร์ต / container / service / เว็บบนเครื่อง)"""
    web = request.args.get("web") or ""
    if _known_target(web) is None:
        return jsonify({"items": []})
    return jsonify({"items": apps_service.suggest(web, request.args.get("type") or "")})


@app.put("/api/apps/<rule_id>")
def apps_update(rule_id):
    body = _json_body()
    if body is None:
        return jsonify({"error": "ต้องส่งเป็น JSON"}), 400
    try:
        ok, out = apps_service.update_app(rule_id, body)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (200 if ok else 404 if out.get("error") == "ไม่พบรายการนี้" else 400)


@app.delete("/api/apps/<rule_id>")
def apps_delete(rule_id):
    try:
        ok, out = apps_service.delete_app(rule_id)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (200 if ok else 404 if out.get("error") == "ไม่พบรายการนี้" else 400)


def _who():
    """(ชื่อผู้ใช้, role) ของ request นี้"""
    return session.get("username", ""), (getattr(g, "role", None) or session.get("role") or "viewer")


@app.get("/api/alert-rules")
def alert_rules_get():
    """กฎแจ้งเตือนที่สร้างเอง + รายการ metric / เครื่อง / กลุ่ม ให้ฟอร์มเลือก (admin)"""
    try:
        return jsonify(alert_rules_service.list_rules(*_who()))
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.put("/api/alert-rules")
def alert_rules_put():
    """บันทึกกฎทั้งรายการ  body: {rules: [...]}"""
    try:
        ok, out = alert_rules_service.save_rules(request.get_json(silent=True) or {}, *_who())
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(out), (200 if ok else 400)


@app.post("/api/alert-rules/test")
def alert_rules_test():
    """ส่งการ์ดตัวอย่างของกฎไป Teams"""
    try:
        status, data = alert_rules_service.send_test(request.get_json(silent=True) or {}, *_who())
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify(data), status


@app.post("/api/alert-rules/preview")
def alert_rules_preview():
    """ทดลองกฎ 1 ข้อกับทุกเครื่อง ณ ตอนนี้ (ไม่บันทึก)"""
    status, data = alert_rules_service.preview(request.get_json(silent=True) or {})
    return jsonify(data), status


@app.post("/api/notify-settings/summary-now")
def notify_summary_now():
    """ส่งการ์ดสรุปสถานะทุกเครื่องไป Teams ตอนนี้ (ผ่าน puller)"""
    try:
        ok, msg = notify_service.request_summary()
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify({"ok": ok, "message": msg, **({} if ok else {"error": msg})}), (200 if ok else 400)


# ============================================================
# Machine details (แท็บ Performance / Hardware / System / Bottleneck)
# ============================================================

_HOST_CACHE = {}        # (kind, web) -> (หมดอายุเมื่อ, data)
HOST_TTL = {"performance": 15, "hardware": 300, "system": 60, "software": 60}


def _host_cached(kind, web, fn):
    now = time.time()
    hit = _HOST_CACHE.get((kind, web))
    if hit and hit[0] > now:
        return hit[1]
    data = fn()
    if len(_HOST_CACHE) > 2000:
        _HOST_CACHE.clear()
    _HOST_CACHE[(kind, web)] = (now + HOST_TTL[kind], data)
    return data


def _known_target(web):
    if web == "central":
        return {"name": "central"}
    return next((t for t in load_web_targets() if t.get("name") == web), None)


@app.get("/api/host/<web>/probe-ports")
def host_probe_ports(web):
    """พอร์ตเว็บที่ cm-probe บนเครื่อง Windows เจอล่าสุด [{port, scheme, code, ms}] (ปุ่มเลือกพอร์ตในหน้า Apps & Web)"""
    if _known_target(web) is None:
        return jsonify({"error": "not found"}), 404
    try:
        data = host_details.probe_ports(web)
    except Exception as e:
        print(f"[HOST] probe-ports {web}: {e!r}")
        return jsonify({"error": f"อ่านข้อมูลจาก Prometheus ไม่ได้: {e}"}), 502
    if data is None:
        return jsonify({"error": "ไม่มีการตั้งค่า metrics ของเครื่องนี้"}), 404
    return jsonify(data)


@app.get("/api/host/<web>/<kind>")
def host_detail(web, kind):
    """kind = performance | hardware | system | software | bottleneck   ค่าที่ไม่มี = null + รายการ unsupported"""
    target = _known_target(web)
    if target is None:
        return jsonify({"error": "not found"}), 404
    if kind == "bottleneck":
        rows = [s for s in sensors_service.get_sensors(web=web)["sensors"] if s.get("kind") == "bottleneck"]
        return jsonify({"web": web, "bottlenecks": rows,
                        "options": sensors_service.load_sensors().get("options")})
    fns = {"performance": lambda: host_details.performance(web),
           "hardware": lambda: host_details.hardware(web),
           "system": lambda: host_details.system(web, target),
           "software": lambda: host_details.software(web)}
    if kind not in fns:
        return jsonify({"error": "unknown kind"}), 404
    try:
        data = _host_cached(kind, web, fns[kind])
    except Exception as e:
        print(f"[HOST] {kind} {web}: {e!r}")
        return jsonify({"error": f"อ่านข้อมูลจาก Prometheus ไม่ได้: {e}"}), 502
    if data is None:
        return jsonify({"error": "ไม่มีการตั้งค่า metrics ของเครื่องนี้"}), 404
    return jsonify(data)


@app.get("/api/overview")
def overview():
    """ตาราง Server Status ด้านบนของ dashboard (ดู overview_service.py)"""
    return jsonify(get_overview())


_LIVE_CACHE = {}        # web -> (หมดอายุเมื่อ, status, data)
LIVE_TTL = 20


@app.get("/api/live-processes")
def live_processes():
    """CPU ต่อ core + process ที่รันบนแต่ละ core + top memory ของเครื่อง ณ ตอนนี้ (ไม่เก็บ)"""
    web = request.args.get("web", "")
    if not web:
        return jsonify({"error": "web required"}), 400

    now = time.time()
    cached = _LIVE_CACHE.get(web)
    if cached and cached[0] > now:
        return jsonify(cached[2]), cached[1]

    if agent_service.agent_target(web):
        # Windows: ไม่มี SSH -> ใช้ process จาก Prometheus (windows_process_*) อัปเดตทุก ~15 วินาที
        from datetime import datetime, timezone
        from metrics_service import get_windows_processes
        procs = get_windows_processes(_labels(get_web_prometheus_config(web)))
        data = {"time": datetime.now(timezone.utc).isoformat(),
                "memory_top": sorted([p for p in procs if p.get("memory") is not None],
                                     key=lambda p: p["memory"], reverse=True)[:10],
                "io_top": sorted([p for p in procs if p.get("io_mb") is not None],
                                 key=lambda p: p["io_mb"], reverse=True)[:10]}
        _LIVE_CACHE[web] = (now + LIVE_TTL, 200, data)
        return jsonify(data), 200

    try:
        status, data = puller_get("/processes", {"target": resolve_web_name(web)})
    except Exception as e:
        status, data = 502, {"error": f"ติดต่อ puller ไม่ได้: {e}"}

    _LIVE_CACHE[web] = (now + LIVE_TTL, status, data)
    return jsonify(data), status


@app.get("/api/metrics")
def metrics():
    web = request.args.get("web", "web-01")
    return jsonify(get_current_metrics(web))


@app.get("/api/metrics/history")
def metrics_history():
    """กราฟย้อนหลัง

    Query: web, start, end (unix วินาที; end ปริยาย = ตอนนี้, start ปริยาย = end - 15 นาที)
    step คำนวณให้ได้ประมาณ 120 จุดต่อกราฟ (อย่างน้อย 15 วินาที)
    """
    web = request.args.get("web", "web-01")

    try:
        end = float(request.args.get("end") or time.time())
        start = float(request.args.get("start") or (end - 900))
    except ValueError:
        return jsonify({"error": "invalid range"}), 400

    if end <= start or end - start > MAX_HISTORY_SECONDS:
        return jsonify({"error": "invalid range"}), 400

    step = max(15, int((end - start) / 120))

    # set = ชุดกราฟ (main/cpu/io/net/load) ; compare=1 = แนบ "เมื่อวานเวลาเดียวกัน"
    name = request.args.get("set", "main")
    if name not in HISTORY_SETS:
        return jsonify({"error": "invalid set"}), 400
    names = HISTORY_SETS[name]

    out = {
        "web": web,
        "set": name,
        "start": start,
        "end": end,
        "step": step,
        "series": get_metrics_history(web, start, end, step, names),
    }
    if request.args.get("compare") == "1":
        out["compare"] = get_metrics_history(web, start, end, step, names, shift=86400)
    if name == "load":
        out["cores"] = get_cpu_cores(web)
    return jsonify(out)


def _range_args():
    """start/end (unix วินาที) จาก query string ไม่ใส่ = None (ไม่จำกัด)"""
    def num(key):
        try:
            v = request.args.get(key)
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None
    return num("start"), num("end")


@app.get("/api/log-markers")
def log_markers():
    """จุด ERROR/WARN สำหรับวางบนเส้นเวลาของกราฟ  Query: web, start, end (unix วินาที)"""
    web = request.args.get("web", "web-01")
    start, end = _range_args()
    return jsonify(get_log_markers(web, start, end))


@app.get("/api/summary")
def summary():
    """ข้อมูลรวมของเครื่องสำหรับแถบด้านบนของ dashboard"""
    web = request.args.get("web", "web-01")

    if agent_service.agent_target(web):
        st = agent_service.status(web)
        current = get_current_metrics(web) if st["online"] else {}
        return jsonify({"web": web, "kind": "agent", "status": "online" if st["online"] else "offline",
                        "last_pull": st["last_seen"], "problem_count": get_problem_count(web),
                        "errors_15m": get_errors_15m(web),
                        "current_state": classify_state(current) if st["online"] else "DOWN",
                        "metrics": {k: current.get(k) for k in ("cpu", "memory", "disk", "load_1m")}})

    current = get_current_metrics(web)
    config = get_web_prometheus_config(web)

    up = prom_value(f'up{{job="{config["job"]}"}}') if config else None

    # สถานะเดียวกับตาราง WEB status: ใช้ผลของ sensor (รวม ping / disk latency / bottleneck) ถ้ามี
    state = classify_state(current)
    try:
        worst = (sensors_service.target_states().get(web) or {}).get("worst")
        state = {"down": "DOWN", "error": "PROBLEM", "warning": "WARNING", "unknown": "WARNING",
                 "ok": "HEALTHY"}.get(worst, state)
    except Exception as e:
        print(f"[SUMMARY] sensor state {web}: {e!r}")

    return jsonify({
        "web": web,
        "status": "online" if up == 1 else "offline",
        "last_pull": last_pull_time(web),
        "problem_count": get_problem_count(web),
        "errors_15m": get_errors_15m(web),
        "current_state": state,
        "metrics": current,
    })


# ============================================================
# log
# ============================================================

@app.get("/api/problems")
def problems():
    """รายการ log ของเครื่อง (ใหม่ -> เก่า) size ปริยาย 200 สูงสุด 1000
    web=all (หรือว่าง) = log ของทุกเครื่องรวมกัน

    ตัวกรอง (ไม่ใส่ = ทั้งหมด)
        level=ERROR,WARN   เฉพาะระดับที่เลือก (ERROR / WARN / INFO)
        type=application   เฉพาะชนิด log (resource log.source: application / loginuser / logapp / other)
    """
    web = request.args.get("web", "all")

    try:
        size = min(int(request.args.get("size", "200")), 1000)
    except Exception:
        size = 200

    start, end = _range_args()

    return jsonify(get_logs(
        web,
        size,
        levels=request.args.get("level", ""),
        types=request.args.get("type", ""),
        start=start,
        end=end,
        with_metrics=request.args.get("with_metrics") == "1",
        include_acked=request.args.get("acked") == "1",
    ))


@app.post("/api/logs/ack")
def logs_ack():
    """รับทราบ log (ซ่อนจากตาราง/ตัวนับ)  body: {"ids": [...], "note": "...", "web": "..."}"""
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        n = log_ack_service.acknowledge(body.get("ids"), session.get("username", ""),
                                        body.get("note"), body.get("web"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"acknowledged": n})


@app.post("/api/logs/unack")
def logs_unack():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    return jsonify({"removed": log_ack_service.unacknowledge(body.get("ids"))})


@app.get("/api/log-stats")
def log_stats():
    """จำนวน log ของเครื่องแยกตาม level และ type (นับจากชุดเดียวกับ /api/problems)  web=all = ทุกเครื่อง"""
    web = request.args.get("web", "all")
    start, end = _range_args()
    return jsonify(get_log_stats(web, start, end))


@app.get("/api/info-logs")
def info_logs():
    """INFO สดจากเครื่อง เฉพาะตอนผู้ใช้กดปุ่ม (ไม่เก็บ / ไม่ cache)"""
    web = request.args.get("web", "")
    if not web:
        return jsonify({"error": "web required"}), 400

    try:
        lines = max(1, min(int(request.args.get("lines", "500")), 5000))
    except ValueError:
        lines = 500

    start, end = _range_args()

    try:
        return jsonify(get_info_logs(
            web, lines, source=request.args.get("source") or None, start=start, end=end
        ))
    except Exception as e:
        print(f"[INFO LOGS ERROR] web={web} error={e!r}")
        return jsonify({"error": f"ดึง INFO log จาก puller ไม่ได้: {e}"}), 502


# ============================================================
# Sensor + System log + Activity log (หน้า /events และป้ายบนหัวหน้าเว็บ)
# ============================================================

@app.get("/events")
def events_page():
    return send_from_directory(STATIC_DIR, "events.html")


@app.post("/api/sensors/ack")
def sensors_ack():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        ack = sensors_service.acknowledge(str(body.get("id", "")), session.get("username", ""), body.get("note"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(ack)


@app.post("/api/sensors/unack")
def sensors_unack():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    if not sensors_service.unacknowledge(str(body.get("id", ""))):
        return jsonify({"error": "not acknowledged"}), 404
    return jsonify({"status": "ok"})


@app.get("/api/incidents")
def incidents():
    """ปัญหาที่เกิดแล้ว (หายแล้ว + ยังเป็นอยู่)  hours = ย้อนหลังกี่ชั่วโมง (ปริยาย 24, สูงสุด 90 วัน)"""
    try:
        hours = max(1.0, min(float(request.args.get("hours", "24")), 90 * 24))
    except ValueError:
        hours = 24
    return jsonify({"hours": hours, "incidents": sensors_service.get_incidents(
        hours, web=request.args.get("web") or None,
        include_ongoing=request.args.get("ongoing", "1") == "1")})


@app.get("/api/sensors/history")
def sensors_history():
    data = sensors_service.get_history(request.args.get("id", ""), request.args.get("range", "24h"))
    if data is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.get("/api/overview/history")
def overview_history_get():
    """กราฟภาพรวมทุกเครื่องย้อนหลัง (Status & Logs > Overview)  range = 2d | 30d | 365d"""
    return jsonify(overview_history.get_history(request.args.get("range", "2d")))


@app.get("/api/sensors")
def sensors():
    statuses = [s.strip() for s in request.args.get("status", "").split(",") if s.strip()]
    return jsonify(sensors_service.get_sensors(
        statuses, web=request.args.get("web") or None, history=request.args.get("history") == "1"))


def _float_arg(key):
    try:
        v = request.args.get(key)
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


@app.get("/api/events")
def events():
    kind = request.args.get("kind", "system")
    if kind not in ("system", "activity"):
        return jsonify({"error": "kind must be system or activity"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", "300")), 2000))
    except ValueError:
        limit = 300
    return jsonify({"kind": kind, "entries": audit_service.read_entries(
        kind, limit=limit, since=_float_arg("since"), query=request.args.get("q"),
        level=request.args.get("level") or None)})


@app.get("/api/events/summary")
def events_summary():
    """ตัวเลขบนป้าย: sensor แต่ละสถานะ + จำนวน log ใหม่กว่าเวลาที่ผู้ใช้เปิดดูล่าสุด (browser ส่งมา)"""
    out = {"sensors": sensors_service.summary(),
           "new_system": audit_service.count_since("system", _float_arg("since_system") or 0)}
    if role_at_least(getattr(g, "role", None), "admin"):
        out["new_activity"] = audit_service.count_since("activity", _float_arg("since_activity") or 0)
    return jsonify(out)


@app.get("/api/logs/search")
def logs_search():
    """ค้นข้อความใน log  q = คำค้น, web = เครื่อง (ว่าง = ทุกเครื่อง), level / type / start / end เหมือน /api/problems"""
    start, end = _range_args()
    try:
        size = min(int(request.args.get("size", "300")), 1000)
    except ValueError:
        size = 300
    try:
        return jsonify(search_logs(
            request.args.get("q", ""),
            web=request.args.get("web") or None,
            levels=request.args.get("level", ""),
            types=request.args.get("type", ""),
            start=start, end=end, size=size,
        ))
    except Exception as e:
        print(f"[SEARCH ERROR] {e!r}")
        return jsonify({"error": f"ค้นหาไม่สำเร็จ: {e}"}), 502


@app.get("/api/problems/detail")
def problem_detail():
    """log 1 รายการ + สถานะเครื่อง ณ เวลาที่ log เกิด (ใช้ history ของ puller)"""
    web = request.args.get("web", "web-01")
    entry = get_log_detail(web, request.args.get("id", ""))

    if entry is None:
        return jsonify({"error": "not found"}), 404

    return jsonify(entry)


# ============================================================
# Settings: จัดการ targets ใน puller.yaml
# ============================================================

def _json_body():
    """บังคับ Content-Type: application/json (ฟอร์มข้ามเว็บส่งแบบนี้ไม่ได้ = กัน CSRF อีกชั้น)"""
    if not request.is_json:
        return None
    return request.get_json(silent=True)


def _config_error(e):
    """อ่าน/เขียน config/puller.yaml ไม่ได้ (ไม่มีไฟล์ / สิทธิ์ไม่พอ / YAML เสีย) -> ข้อความที่อ่านรู้เรื่อง

    เดิมได้แค่ "INTERNAL SERVER ERROR" ไม่รู้ว่าพังที่ไหน หน้า Settings แสดงค่า error นี้ให้เลย
    """
    print(f"[CONFIG ERROR] {type(e).__name__}: {e}")
    return jsonify({
        "error": f"อ่าน/เขียน config/puller.yaml ไม่ได้ ({type(e).__name__}: {e}) "
                 f"- ตรวจว่ามีไฟล์ อ่านได้ และ YAML ถูกต้อง"
    }), 500


def _bad_request(exc):
    return jsonify({"error": "validation", "fields": exc.fields}), exc.status


@app.get("/settings")
def settings_page():
    """หน้า Settings (frontend/settings.html)"""
    return send_from_directory(STATIC_DIR, "settings.html")


@app.get("/api/targets")
def targets_list():
    try:
        targets = targets_service.list_targets()
    except (OSError, YAMLError) as e:
        return _config_error(e)
    return jsonify({
        "targets": targets,
        "public_key": targets_service.public_key(),
    })


@app.post("/api/targets")
def targets_add():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        return jsonify(targets_service.add_target(body)), 201
    except ValidationError as e:
        return _bad_request(e)
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.put("/api/targets/<name>")
def targets_update(name):
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        if _who()[1] == "admin":
            result = targets_service.update_target(name, body)
        else:                                       # operator: เฉพาะการแจ้งเตือน + log
            result = targets_service.update_monitoring(name, body)
    except ValidationError as e:
        return _bad_request(e)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


@app.delete("/api/targets/<name>")
def targets_delete(name):
    try:
        deleted = targets_service.delete_target(name)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "ok"})


@app.post("/api/targets/<name>/deploy-key")
def targets_deploy_key(name):
    """วาง public key ลงเครื่อง target (เข้าด้วยรหัสผ่านครั้งเดียว ไม่เก็บรหัส) ดู targets_service.deploy_key"""
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        result = targets_service.deploy_key(name, body)
    except ValidationError as e:
        return _bad_request(e)
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


@app.post("/api/targets/host-key")
def targets_host_key():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        keys = targets_service.scan_host_key(str(body.get("host", "")).strip(), body.get("port"))
    except ValidationError as e:
        return _bad_request(e)
    return jsonify({"keys": [{"type": k["type"], "fingerprint": k["fingerprint"]} for k in keys]})


@app.put("/api/targets/<name>/pause")
def targets_pause(name):
    """Pause / Resume เครื่อง  body: {"paused": true|false}"""
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        result = targets_service.set_paused(name, bool(body.get("paused")))
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


# ============================================================
# Web / SSL checks
# ============================================================

@app.get("/api/checks")
def checks_list():
    try:
        return jsonify({"checks": checks_service.get_checks()})
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.post("/api/checks")
def checks_add():
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        return jsonify(checks_service.add_check(body)), 201
    except ValidationError as e:
        return _bad_request(e)
    except (OSError, YAMLError) as e:
        return _config_error(e)


@app.put("/api/checks/<name>")
def checks_update(name):
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        result = checks_service.update_check(name, body)
    except ValidationError as e:
        return _bad_request(e)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


@app.put("/api/checks/<name>/pause")
def checks_pause(name):
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        result = checks_service.set_check_paused(name, bool(body.get("paused")))
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if result is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


@app.delete("/api/checks/<name>")
def checks_delete(name):
    try:
        deleted = checks_service.delete_check(name)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": "ok"})


@app.get("/api/targets/<name>/services")
def targets_watch_services_get(name):
    """service ที่เลือกให้แสดงในการ์ด Service ของเครื่องนี้ ([] = แสดงทั้งหมด)"""
    try:
        services = targets_service.get_watch_services(name)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if services is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"web": name, "services": services})


@app.put("/api/targets/<name>/services")
def targets_watch_services_put(name):
    """บันทึก service ที่จะแสดง  body: {"services": ["nginx.service", ...]}  ([] = แสดงทั้งหมด)"""
    body = _json_body()
    if body is None:
        return jsonify({"error": "json_required"}), 415
    try:
        services = targets_service.set_watch_services(name, body.get("services"))
    except ValidationError as e:
        return _bad_request(e)
    except (OSError, YAMLError) as e:
        return _config_error(e)
    if services is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"web": name, "services": services})


# ============================================================
# รันตรง (ใช้ตอนพัฒนา; production ใช้ gunicorn)
# ============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
