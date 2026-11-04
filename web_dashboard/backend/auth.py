"""
auth.py - ตรวจชื่อผู้ใช้/รหัสผ่านของหน้าเว็บ
============================================

มี 2 แหล่งบัญชี
    1) บัญชีหลักจาก docker/.env  (CENTRAL_AUTH_USERNAME / CENTRAL_AUTH_PASSWORD, ปริยาย admin/admin)
       เป็น role admin เสมอ ใช้ได้ตลอด (กันล็อกตัวเองออก)
       เปลี่ยน: แก้ docker/.env แล้ว  cd docker && docker compose up -d --force-recreate central-api
    2) ผู้ใช้ที่ admin สร้างในหน้า Settings -> Users  (data/state/users.json ดู users_service.py)

ใครเรียก
    api.py : POST /api/login -> check_credentials() คืน role (หรือ None)
    การป้องกันหน้า/API + ตรวจสิทธิ์ตาม role อยู่ที่ api.py ฟังก์ชัน require_authentication
"""

import hmac
import os

import users_service

# ค่าเริ่มต้น admin/admin (ถ้าใน .env ไม่ได้ตั้ง หรือปล่อยว่าง)
AUTH_USERNAME = os.getenv("CENTRAL_AUTH_USERNAME", "").strip() or "admin"
AUTH_PASSWORD = os.getenv("CENTRAL_AUTH_PASSWORD", "").strip() or "admin"


def check_credentials(username, password):
    """ถูกต้อง -> role ("admin" / "operator" / "viewer")  ผิด -> None

    บัญชีจาก .env ใช้ hmac.compare_digest (เทียบแบบเวลาคงที่) และตรวจทั้งชื่อและรหัสเสมอ
    """
    user_ok = hmac.compare_digest(
        str(username).encode("utf-8"), AUTH_USERNAME.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        str(password).encode("utf-8"), AUTH_PASSWORD.encode("utf-8")
    )

    if user_ok and pass_ok:
        return "admin"
    if user_ok:
        return None          # ชื่อตรงบัญชีหลักแต่รหัสผิด -> ไม่ลองใน users.json

    return users_service.authenticate(username, password)


def session_role(username, stored_role):
    """role ที่มีผล ณ ตอนนี้ของ session (ผู้ใช้ถูกลบ/ลดสิทธิ์ -> มีผลทันที)"""
    if username == AUTH_USERNAME:
        return "admin"
    role = users_service.current_role(username)
    return role
