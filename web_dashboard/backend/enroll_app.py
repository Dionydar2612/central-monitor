"""
enroll_app.py - central-agent-enroll (HTTPS :4319) ให้เครื่อง Windows ติดตั้ง agent ด้วยคำสั่งบรรทัดเดียว
======================================================================================================

ไม่มีหน้าเว็บ / ไม่มีการ login (เปิดให้เครื่องในวง network) ทุกอย่างต้องมี enroll code หรือ token ของเครื่อง
    GET  /agent/i/<code>          สคริปต์ bootstrap (PowerShell) ของ code นี้
    POST /agent/register          {code, host} -> {name, token, logs, channels, hash, ca_pem, ...}  (ใช้ code)
    GET  /agent/files/<ชื่อ>       ไฟล์ติดตั้ง: สคริปต์ใน agent/windows + Fluent Bit zip + windows_exporter exe
                                  (เก็บไว้ที่ data/agent-files ครั้งแรกดาวน์โหลดจากอินเทอร์เน็ตให้เอง)
    GET  /agent/config            Authorization: Bearer <token ของเครื่อง> -> ค่าตั้งล่าสุด (agent ถามทุก 5 นาที)
"""

import os
import re
import threading
import urllib.request

from flask import Flask, Response, abort, jsonify, request, send_file

import agent_auth
from settings import load_web_targets

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

AGENT_DIR = os.getenv("CENTRAL_AGENT_DIR", "/app/agent")
FILES_DIR = os.getenv("CENTRAL_AGENT_FILES", "/data/agent-files")
FB_VERSION = os.getenv("CENTRAL_FLUENTBIT_VERSION", "5.1.5")
EXP_VERSION = os.getenv("CENTRAL_EXPORTER_VERSION", "0.32.1")
SCRIPTS = ("install-fluentbit.ps1", "uninstall-fluentbit.ps1", "cm-agent-sync.ps1")
UPSTREAM = {
    f"fluent-bit-{FB_VERSION}-win64.zip": f"https://packages.fluentbit.io/windows/fluent-bit-{FB_VERSION}-win64.zip",
    f"windows_exporter-{EXP_VERSION}-amd64.exe": ("https://github.com/prometheus-community/windows_exporter/releases/"
                                                  f"download/v{EXP_VERSION}/windows_exporter-{EXP_VERSION}-amd64.exe"),
}
MIN_SIZE = 3 * 1024 * 1024
_dl_lock = threading.Lock()

agent_auth.init()


def _target(web):
    return next((t for t in load_web_targets() if t.get("name") == web and str(t.get("type") or "") == "agent"), None)


def _ps(s):
    """สตริง PowerShell แบบ single quote"""
    return "'" + str(s).replace("'", "''") + "'"


@app.get("/agent/i/<code>")
def bootstrap(code):
    v = agent_auth.code_entry(code)
    if not v:
        return Response("throw 'Central Monitor: code หมดอายุหรือไม่ถูกต้อง - สร้างคำสั่งใหม่ในหน้า Settings'\n",
                        mimetype="text/plain; charset=utf-8", status=404)
    central = v.get("central") or request.host.split(":")[0]
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_bootstrap.ps1"), encoding="utf-8").read()
    body = (tpl.replace("__CENTRAL__", _ps(central)).replace("__ENROLL_PORT__", str(agent_auth.ENROLL_PORT))
            .replace("__AGENT_PORT__", str(agent_auth.AGENT_PORT)).replace("__CODE__", _ps(code.upper()))
            .replace("__THUMB__", _ps(agent_auth.thumbprint())).replace("__WEB__", _ps(v["web"]))
            .replace("__FB_VERSION__", _ps(FB_VERSION)).replace("__EXP_VERSION__", _ps(EXP_VERSION)))
    return Response(body, mimetype="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.post("/agent/register")
def register():
    d = request.get_json(silent=True) or {}
    web, token = agent_auth.register(d.get("code"), d.get("host"))
    if not web:
        return jsonify({"error": token}), 403
    t = _target(web)
    if not t:
        return jsonify({"error": f"ไม่พบเครื่อง {web} ในหน้า Settings แล้ว"}), 404
    print(f"[ENROLL] {web} registered from {d.get('host')} ({request.remote_addr})")
    return jsonify({**agent_auth.config_of(t), "token": token, "ca_pem": agent_auth.ca_pem(),
                    "thumbprint": agent_auth.thumbprint(), "agent_port": agent_auth.AGENT_PORT,
                    "enroll_port": agent_auth.ENROLL_PORT})


@app.get("/agent/config")
def config():
    auth = request.headers.get("Authorization", "")
    web = agent_auth.web_of_token(auth[7:].strip() if auth.startswith("Bearer ") else "")
    if not web:
        return jsonify({"error": "token ถูกยกเลิก หรือไม่ถูกต้อง"}), 401
    t = _target(web)
    if not t:
        return jsonify({"error": "ไม่พบเครื่องนี้ในหน้า Settings"}), 404
    agent_auth.touch_sync(web)
    return jsonify(agent_auth.config_of(t))


def _fetch(name):
    """ไฟล์ติดตั้งขนาดใหญ่: มีใน data/agent-files -> ใช้เลย  ไม่มี -> ดาวน์โหลดจากอินเทอร์เน็ตครั้งเดียวแล้วเก็บไว้"""
    path = os.path.join(FILES_DIR, name)
    if os.path.isfile(path) and os.path.getsize(path) >= MIN_SIZE:
        return path
    with _dl_lock:
        if os.path.isfile(path) and os.path.getsize(path) >= MIN_SIZE:
            return path
        os.makedirs(FILES_DIR, exist_ok=True)
        tmp = path + ".part"
        print(f"[ENROLL] downloading {UPSTREAM[name]}")
        try:
            with urllib.request.urlopen(UPSTREAM[name], timeout=120) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if os.path.getsize(tmp) < MIN_SIZE:
                raise OSError("ไฟล์เล็กผิดปกติ")
            os.replace(tmp, path)
        except Exception as e:
            print(f"[ENROLL] download {name} failed: {e!r}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
    return path


@app.get("/agent/files/<name>")
def files(name):
    if name in SCRIPTS:
        path = os.path.join(AGENT_DIR, "windows", name)
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype="text/plain", max_age=0)
    if name == "central-ca.pem":
        return Response(agent_auth.ca_pem(), mimetype="text/plain")
    if name not in UPSTREAM or not re.match(r"^[\w.-]+$", name):
        abort(404)
    path = _fetch(name)
    if not path:
        return jsonify({"error": "central ยังไม่มีไฟล์นี้ และดาวน์โหลดไม่ได้"}), 503
    return send_file(path, mimetype="application/octet-stream", max_age=0)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})
