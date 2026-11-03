"""
config.py - ค่าตั้งต้นและตัวอ่านไฟล์ config ของ puller
=======================================================

หน้าที่
    รวม "ที่อยู่ไฟล์/โฟลเดอร์" และ "การอ่าน config/puller.yaml" ไว้ที่เดียว
    โมดูลอื่นใน puller ไม่ต้องรู้ว่า path จริงอยู่ที่ไหน แค่ import จากไฟล์นี้

ทำไมต้องแยก
    path เดียวกันถูกใช้หลายโมดูล (checkpoint, log_puller, system_info,
    prometheus_targets) ถ้ากระจายอยู่หลายไฟล์ จะแก้ยากและหลุดไม่ตรงกัน

เชื่อมกับอะไร
    - config/puller.yaml        (ถูก mount เข้า container ที่ /app/config)
    - data/buffer               (otel อ่านโฟลเดอร์นี้ต่อ)  -> BUFFER_DIR
    - data/state                (checkpoint + snapshot ของเครื่อง) -> STATE_DIR
    - keys/                     (SSH key + known_hosts) -> SSH_KEY_PATH, SSH_KNOWN_HOSTS
    ทุกค่าเปลี่ยนได้ผ่าน environment variable (ตั้งใน docker/docker-compose.yml
    หรือใน systemd unit) ค่าเริ่มต้นคือ path ภายใน container
"""

import os

import yaml

# ---------------------------------------------------------------------------
# ที่อยู่ไฟล์/โฟลเดอร์ (override ได้ด้วย environment variable)
# ---------------------------------------------------------------------------

# ไฟล์ config หลัก: รายชื่อเครื่อง, path log, ความถี่ดึง, retention
CONFIG_FILE = os.environ.get("CENTRAL_CONFIG_FILE", "/app/config/puller.yaml")

# โฟลเดอร์พักไฟล์ log ที่ดึงมา -> otel (filelog) อ่านโฟลเดอร์นี้ต่อ
BUFFER_DIR = os.environ.get("CENTRAL_BUFFER_DIR", "/data/buffer")

# โฟลเดอร์เก็บสถานะของ puller (checkpoint + snapshot ของเครื่อง)
STATE_DIR = os.environ.get("CENTRAL_STATE_DIR", "/data/state")

# checkpoint = "อ่านไฟล์ log ของแต่ละเครื่องถึง byte ที่เท่าไหร่แล้ว"
CHECKPOINT_FILE = os.path.join(STATE_DIR, "checkpoints.json")

# snapshot สถานะเครื่องล่าสุด (<เครื่อง>.json) + ประวัติ (history/<เครื่อง>/*.json)
# web_dashboard/backend/system_state.py อ่านโฟลเดอร์นี้ -> ต้องตรงกัน
SYSTEM_DIR = os.path.join(STATE_DIR, "system")

# SSH key / known_hosts (ใน container mount มาจาก ./keys)
SSH_KEY_PATH = os.environ.get("CENTRAL_SSH_KEY", "/root/.ssh/id_ed25519")
# ว่าง = ใช้ known_hosts ปริยายของ user ที่รันอยู่
SSH_KNOWN_HOSTS = os.environ.get("CENTRAL_SSH_KNOWN_HOSTS", "")

# พอร์ตที่ puller เปิด /metrics ให้ Prometheus มา scrape (ดู metrics.py)
METRICS_PORT = 8000


# ---------------------------------------------------------------------------
# ตัวช่วยอ่าน config
# ---------------------------------------------------------------------------

def load_config():
    """อ่าน puller.yaml ใหม่ทุกครั้งที่เรียก

    ถูกเรียกตอนเริ่มรอบดึงทุกรอบ จึงแก้ puller.yaml แล้วมีผลในรอบถัดไป
    โดยไม่ต้อง restart (รวมถึง scheduler.interval ด้วย)
    """
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f) or {}


def parse_interval(value):
    """แปลงข้อความเวลา เช่น "30s", "5m", "1h", "7d" ให้เป็นวินาที

    รับเลขล้วน (int หรือ "90") ก็ได้ ถือเป็นวินาที
    ใช้กับ scheduler.interval, scheduler.target_stagger และ storage.*_retention
    """
    if isinstance(value, int):
        return value

    value = str(value).strip().lower()

    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    if value and value[-1] in units:
        return int(value[:-1]) * units[value[-1]]

    return int(value)
