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
    GET  /api/summary?web=        สรุปเครื่อง: สถานะ, Last pull, จำนวน problem, metrics
    GET  /api/metrics?web=        metrics ตอนนี้ + service/process/event
    GET  /api/metrics/history     กราฟย้อนหลัง (web, start, end เป็น unix วินาที)
    GET  /api/problems?web=&size=&level=&type=   รายการ log ของเครื่อง (level = ERROR,WARN,INFO / type = ชนิด log)
    GET  /api/log-stats?web=      จำนวน log แยกตาม level และ type (ตัวเลขบนปุ่มตัวกรอง)
    GET  /api/log-markers?web=&start=&end=   จุด ERROR/WARN บนเส้นเวลาของกราฟ
         (/api/problems และ /api/log-stats รับ start/end ได้ด้วย = เฉพาะช่วงนั้น)
    GET  /api/problems/detail     log 1 รายการ + สถานะเครื่อง ณ เวลานั้น (web, id)
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

import time

from ruamel.yaml import YAMLError
from flask import Flask, jsonify, redirect, request, send_from_directory, session

import settings
from auth import check_credentials
from clients import prom_value
from logs_service import (
    get_errors_15m,
    get_info_logs,
    get_log_detail,
    get_log_markers,
    get_log_stats,
    get_logs,
    get_problem_count,
)
from metrics_service import (
    classify_state,
    get_current_metrics,
    HISTORY_SETS,
    get_cpu_cores,
    get_metrics_history,
    get_web_prometheus_config,
)
from overview_service import get_overview
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

    if session.get("authenticated"):
        return None

    if request.path.startswith("/api/"):
        return jsonify({"error": "authentication_required"}), 401

    return redirect("/login")


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

    if not check_credentials(data.get("username", ""), data.get("password", "")):
        return jsonify({"error": "invalid_credentials"}), 401

    session["authenticated"] = True
    session["username"] = data.get("username", "")

    return jsonify({"status": "ok"})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.get("/api/me")
def current_user():
    """ใครล็อกอินอยู่ (หน้าเว็บโชว์ "Logged in as ...")"""
    if not session.get("authenticated"):
        return jsonify({"authenticated": False}), 401

    return jsonify({"authenticated": True, "username": session.get("username", "")})


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


@app.get("/api/overview")
def overview():
    """ตาราง Server Status ด้านบนของ dashboard (ดู overview_service.py)"""
    return jsonify(get_overview())


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

    current = get_current_metrics(web)
    config = get_web_prometheus_config(web)

    up = prom_value(f'up{{job="{config["job"]}"}}') if config else None

    return jsonify({
        "web": web,
        "status": "online" if up == 1 else "offline",
        "last_pull": last_pull_time(web),
        "problem_count": get_problem_count(web),
        "errors_15m": get_errors_15m(web),
        "current_state": classify_state(current),
        "metrics": current,
    })


# ============================================================
# log
# ============================================================

@app.get("/api/problems")
def problems():
    """รายการ log ของเครื่อง (ใหม่ -> เก่า) size ปริยาย 200 สูงสุด 1000

    ตัวกรอง (ไม่ใส่ = ทั้งหมด)
        level=ERROR,WARN   เฉพาะระดับที่เลือก (ERROR / WARN / INFO)
        type=application   เฉพาะชนิด log (resource log.source: application / loginuser / logapp / other)
    """
    web = request.args.get("web", "web-01")

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
    ))


@app.get("/api/log-stats")
def log_stats():
    """จำนวน log ของเครื่องแยกตาม level และ type (นับจากชุดเดียวกับ /api/problems)"""
    web = request.args.get("web", "web-01")
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
        result = targets_service.update_target(name, body)
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
