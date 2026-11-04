"""
settings.py - ค่าตั้งต้นทั้งหมดของ web_dashboard (backend)
==========================================================

หน้าที่
    รวม "ค่าที่มาจาก environment variable และ path ของไฟล์" ไว้ที่เดียว
    โมดูลอื่นไม่ต้องเรียก os.getenv เอง และรู้ได้ทันทีว่าระบบฝั่งเว็บ
    ต้องการค่าอะไรบ้าง

ค่ามาจากไหน
    docker/docker-compose.yml (service central-api) + docker/.env
    ตัวแปรที่ "ต้องมี" (ไม่มีหรือเป็น CHANGE_ME -> โปรแกรมไม่ยอมเริ่ม เพื่อไม่ให้เปิดใช้
    ด้วยรหัสผ่านตั้งต้น):
        CENTRAL_SECRET_KEY            ใช้เซ็น session cookie
        OPENSEARCH_ADMIN_PASSWORD     รหัสผ่านเข้า OpenSearch
        (CENTRAL_AUTH_USERNAME / CENTRAL_AUTH_PASSWORD ไม่บังคับ: ค่าเริ่มต้น admin/admin ดู auth.py)

เชื่อมกับอะไร
    - OpenSearch  (log)      -> ใช้ใน clients.py
    - Prometheus  (metrics)  -> ใช้ใน clients.py
    - config/puller.yaml     -> รายชื่อเครื่อง (load_web_targets)
    - data/state             -> ไฟล์ที่ puller เขียน (ดู system_state.py)
"""

import os

import yaml


def require_env(name):
    """อ่าน environment variable ที่จำเป็น ถ้าไม่มี/ยังเป็นค่า placeholder จะ raise

    ใช้ทั้งใน settings.py และ auth.py (เดิมเขียนซ้ำ 2 ที่)
    """
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name} "
            f"(set it in docker/.env - see docker/.env.example)"
        )

    if value.upper() in ("CHANGE_ME", "CHANGE-ME", "CHANGEME"):
        raise RuntimeError(
            f"{name} still has the placeholder value - "
            f"run scripts/init-env.sh or edit docker/.env"
        )

    return value


# --- ไฟล์/โฟลเดอร์ -----------------------------------------------------------

# โฟลเดอร์ของหน้าเว็บ (web.Dockerfile คัดลอก web_dashboard/frontend/ มาไว้ที่นี่)
STATIC_DIR = os.getenv("CENTRAL_STATIC_DIR", "/app/static")

# puller.yaml (mount จาก ./config) ใช้อ่านรายชื่อเครื่องและ label ของ Prometheus
CONFIG_FILE = os.getenv("CENTRAL_CONFIG_FILE", "/app/config/puller.yaml")

# โฟลเดอร์สถานะที่ puller เขียน (mount จาก ./data/state) dashboard อ่านอย่างเดียว
STATE_DIR = os.getenv("CENTRAL_STATE_DIR", "/data/state")

# SSH key / known_hosts (docker-compose mount เฉพาะ 2 ไฟล์นี้เข้า central-api)
#   known_hosts : เขียนได้ (หน้า Settings เก็บ host key ให้)   .pub : อ่านอย่างเดียว (โชว์ให้ copy)
KEYS_DIR = os.getenv("CENTRAL_KEYS_DIR", "/run/keys")
KNOWN_HOSTS_FILE = os.path.join(KEYS_DIR, "known_hosts")
PUBLIC_KEY_FILE = os.path.join(KEYS_DIR, "central_ed25519.pub")
# (ไม่บังคับ) จำกัด IP ที่ใช้ key นี้ได้ ใส่เป็น from="..." หน้า key ตอนวางลงเครื่อง target เช่น 10.0.0.5
KEY_FROM = os.getenv("CENTRAL_KEY_FROM", "").strip()

# ไฟล์ที่ให้ Prometheus (file_sd) และไฟล์ "สั่งดึงเดี๋ยวนี้" ที่ puller เฝ้าดู
PROMETHEUS_TARGET_FILE = os.getenv(
    "CENTRAL_PROMETHEUS_TARGET_FILE", "/data/prometheus/web-nodes.json"
)
PULL_TRIGGER_FILE = os.path.join(STATE_DIR, "pull-now.json")
TARGETS_LOCK_FILE = os.path.join(STATE_DIR, ".targets.lock")

# --- puller (ปุ่ม "Load INFO log": ดึง INFO สดผ่าน puller ซึ่งถือ SSH key) ---------
PULLER_URL = os.getenv("PULLER_URL", "http://central-puller:8001").rstrip("/")
PULLER_TOKEN = os.getenv("CENTRAL_PULLER_TOKEN", "")

# --- session / cookie --------------------------------------------------------

SECRET_KEY = require_env("CENTRAL_SECRET_KEY")

# ตั้ง CENTRAL_COOKIE_SECURE=true เมื่อเข้าเว็บผ่าน HTTPS (เช่น หลัง nginx)
COOKIE_SECURE = os.getenv("CENTRAL_COOKIE_SECURE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# --- OpenSearch (แหล่ง log) --------------------------------------------------

OPENSEARCH = os.getenv("OPENSEARCH_URL", "https://opensearch:9200").rstrip("/")
OS_USER = os.getenv("OPENSEARCH_USER", "admin")
OS_PASSWORD = require_env("OPENSEARCH_ADMIN_PASSWORD")

# --- Prometheus (แหล่ง metrics) ---------------------------------------------
#   bundled Prometheus : http://prometheus:9090   (ปริยาย)
#   Prometheus บน server: เช่น http://host.docker.internal:9090

PROMETHEUS = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")

# label ใน Prometheus ของ "เครื่อง central เอง" (รายการ "central" ใน UI)
CENTRAL_NODE_JOB = os.getenv("CENTRAL_NODE_JOB", "central")
CENTRAL_NODE_INSTANCE = os.getenv("CENTRAL_NODE_INSTANCE", "node-exporter:9100")


def load_web_targets():
    """อ่านรายชื่อเครื่อง (targets) จาก puller.yaml  (อ่านใหม่ทุกครั้ง -> แก้แล้วเห็นเลย)"""
    try:
        with open(CONFIG_FILE, "r") as f:
            config = yaml.safe_load(f) or {}

        return config.get("targets", [])

    except Exception as e:
        print(f"[CONFIG ERROR] {e}")
        return []


def resolve_web_name(web):
    """ชื่อเครื่องใน puller.yaml ที่ตรงกับ "web" ที่รับมา

    ชื่อใน log อาจเป็น hostname จริงของเครื่อง (เช่น 30K3PH4) ซึ่งไม่ตรงกับชื่อ target
    ถ้าเครื่องนั้นมี `aliases: [30K3PH4]` ใน puller.yaml จะแปลงให้เป็นชื่อ target
    ไม่เจอ alias -> คืนค่าเดิม  (เดิมเขียน mapping ชื่อเครื่องไว้ตรง ๆ ในโค้ด)
    """
    targets = load_web_targets()

    for target in targets:
        if target.get("name") == web:
            return web

    for target in targets:
        aliases = target.get("aliases") or []
        if isinstance(aliases, list) and web in aliases:
            return target.get("name")

    return web
