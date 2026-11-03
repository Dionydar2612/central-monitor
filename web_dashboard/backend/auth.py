"""
auth.py - ตรวจชื่อผู้ใช้/รหัสผ่านของหน้าเว็บ
============================================

หน้าที่
    เก็บ "บัญชีเดียว" ที่เข้าหน้า dashboard ได้ และตรวจรหัสผ่านตอนล็อกอิน
    ค่าเริ่มต้น: username = admin, password = admin
    อยากเปลี่ยน: ตั้ง CENTRAL_AUTH_USERNAME / CENTRAL_AUTH_PASSWORD ใน docker/.env
    แล้ว  cd docker && docker compose up -d --force-recreate central-api

ใครเรียก
    api.py : POST /api/login -> check_credentials()
    การป้องกันหน้า/API (ต้องล็อกอินก่อน) อยู่ที่ api.py ฟังก์ชัน require_authentication
    (เดิมในไฟล์นี้มี login_required / protect_page ที่ไม่มีใครเรียก -> ลบออกแล้ว)
"""

import hmac
import os

# ค่าเริ่มต้น admin/admin (ถ้าใน .env ไม่ได้ตั้ง หรือปล่อยว่าง)
AUTH_USERNAME = os.getenv("CENTRAL_AUTH_USERNAME", "").strip() or "admin"
AUTH_PASSWORD = os.getenv("CENTRAL_AUTH_PASSWORD", "").strip() or "admin"


def check_credentials(username, password):
    """ตรวจชื่อผู้ใช้และรหัสผ่าน

    ใช้ hmac.compare_digest (เทียบแบบเวลาคงที่) ป้องกันการเดารหัสจากเวลาตอบสนอง
    และตรวจทั้งสองค่าเสมอ ไม่หยุดทันทีเมื่อชื่อผู้ใช้ผิด
    """
    user_ok = hmac.compare_digest(
        str(username).encode("utf-8"), AUTH_USERNAME.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        str(password).encode("utf-8"), AUTH_PASSWORD.encode("utf-8")
    )

    return user_ok and pass_ok
