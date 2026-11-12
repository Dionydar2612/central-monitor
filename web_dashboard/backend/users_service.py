"""
users_service.py - ผู้ใช้หลายคน + สิทธิ์ (role) ของหน้าเว็บ
===========================================================

role (เรียงจากสิทธิ์น้อย -> มาก)
    viewer    ดูได้อย่างเดียว
    operator  + Pause/Resume เครื่องและ check, กด Load INFO log, เลือก service ที่จะแสดง, Acknowledge
              + Custom alert rules (Settings): สร้างกฎได้ และแก้/ลบได้เฉพาะกฎที่ตัวเองสร้าง (owner)
    admin     + หน้า Settings ทั้งหมด (แก้กฎของทุกคนได้) (เพิ่ม/แก้/ลบเครื่อง, web check, จัดการผู้ใช้)

เก็บที่ไหน
    data/state/users.json  (ไม่อยู่ใน git, อยู่ใต้ data/ ที่ถูกสำรองตอน cm-update)
    รหัสผ่านเก็บเป็น PBKDF2-SHA256 (200,000 รอบ + salt สุ่ม) ไม่เก็บรหัสจริง

บัญชีจาก docker/.env (CENTRAL_AUTH_USERNAME / CENTRAL_AUTH_PASSWORD)
    ยังใช้ได้เสมอและเป็น admin  -> กันล็อกตัวเองออกจากระบบ (ลืมรหัสผู้ใช้อื่นก็ยังเข้าได้)
    สร้างผู้ใช้ชื่อเดียวกับบัญชีนี้ใน users.json ไม่ได้

ใช้โดย: auth.py (ตรวจรหัส), api.py (/api/users*, ตรวจสิทธิ์ทุก request)
"""

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from settings import STATE_DIR

USERS_FILE = os.path.join(STATE_DIR, "users.json")
LOCK_FILE = os.path.join(STATE_DIR, ".users.lock")

ROLES = ("viewer", "operator", "admin")
ROLE_LEVEL = {r: i for i, r in enumerate(ROLES)}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{2,40}$")
MIN_PASSWORD = 8
ITERATIONS = 200_000


class UserError(Exception):
    def __init__(self, fields, status=400):
        super().__init__("user")
        self.fields = fields
        self.status = status


def role_at_least(role, needed):
    return ROLE_LEVEL.get(role or "", -1) >= ROLE_LEVEL[needed]


# ------------------------------------------------------------------
# รหัสผ่าน
# ------------------------------------------------------------------

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password, stored):
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        test = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), base64.b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(test, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------
# อ่าน/เขียนไฟล์
# ------------------------------------------------------------------

@contextlib.contextmanager
def _locked():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _load():
    try:
        with open(USERS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(users, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def _public(name, u):
    return {"username": name, "role": u.get("role"), "created_at": u.get("created_at"),
            "updated_at": u.get("updated_at"), "last_login": u.get("last_login")}


# ------------------------------------------------------------------
# ใช้จาก auth / api
# ------------------------------------------------------------------

def authenticate(username, password):
    """ถูก -> role, ผิด/ไม่มีผู้ใช้ -> None  (ไม่รวมบัญชีจาก .env ซึ่ง auth.py ตรวจเอง)"""
    u = _load().get(str(username))
    if not u or not verify_password(password, u.get("password", "")):
        return None
    with contextlib.suppress(OSError), _locked():
        users = _load()
        if username in users:
            users[username]["last_login"] = int(time.time())
            _save(users)
    return u.get("role") if u.get("role") in ROLES else None


def current_role(username):
    """role ปัจจุบันของผู้ใช้ใน users.json (ถูกลบ -> None)  ใช้ตรวจทุก request: ลด/ลบสิทธิ์แล้วมีผลทันที"""
    u = _load().get(str(username))
    return u.get("role") if u else None


def list_users():
    return [_public(n, u) for n, u in sorted(_load().items(), key=lambda kv: kv[0].lower())]


def _check_password(password, errors):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        errors["password"] = f"รหัสผ่านอย่างน้อย {MIN_PASSWORD} ตัวอักษร"


def add_user(data, reserved_name):
    errors = {}
    name = str(data.get("username", "")).strip()
    role = data.get("role")
    if not USERNAME_RE.match(name):
        errors["username"] = "ใช้ A-Z a-z 0-9 _ . @ - ยาว 2-40 ตัว"
    elif name == reserved_name:
        errors["username"] = "ชื่อนี้เป็นบัญชี admin จาก docker/.env อยู่แล้ว"
    if role not in ROLES:
        errors["role"] = "role ต้องเป็น viewer / operator / admin"
    _check_password(data.get("password"), errors)
    if errors:
        raise UserError(errors)
    with _locked():
        users = _load()
        if name in users:
            raise UserError({"username": "มีผู้ใช้นี้แล้ว"})
        now = int(time.time())
        users[name] = {"role": role, "password": hash_password(data["password"]),
                       "created_at": now, "updated_at": now}
        _save(users)
        return _public(name, users[name])


def update_user(name, data):
    """แก้ role และ/หรือ รหัสผ่าน (ส่งเฉพาะช่องที่ต้องการแก้)  ไม่มีผู้ใช้ -> None"""
    errors = {}
    if "role" in data and data["role"] not in ROLES:
        errors["role"] = "role ต้องเป็น viewer / operator / admin"
    if data.get("password"):
        _check_password(data["password"], errors)
    if errors:
        raise UserError(errors)
    with _locked():
        users = _load()
        if name not in users:
            return None
        if "role" in data:
            users[name]["role"] = data["role"]
        if data.get("password"):
            users[name]["password"] = hash_password(data["password"])
        users[name]["updated_at"] = int(time.time())
        _save(users)
        return _public(name, users[name])


def delete_user(name):
    with _locked():
        users = _load()
        if name not in users:
            return False
        del users[name]
        _save(users)
        return True
