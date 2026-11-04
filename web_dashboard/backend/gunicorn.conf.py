"""
gunicorn.conf.py - gunicorn อ่านไฟล์นี้เองอัตโนมัติ (อยู่ใน working dir /app)

HTTPS ในตัว (ไม่ต้องมี nginx)
    ถ้ามีไฟล์ใบรับรองที่ /run/tls/cert.pem + /run/tls/key.pem  (mount มาจาก keys/tls/ บน host)
    หน้าเว็บจะเปิดเป็น https:// ทันทีที่พอร์ตเดิม
    สร้างใบรับรองแบบ self-signed:  ./scripts/make-cert.sh <IP หรือชื่อเครื่อง>
    หรือวางใบรับรองจริงของบริษัท (cert.pem = รวม chain, key.pem) ไว้ใน keys/tls/ เอง
    แล้ว: cd docker && docker compose up -d --force-recreate central-api
"""

import os

CERT = os.environ.get("CENTRAL_TLS_CERT", "/run/tls/cert.pem")
KEY = os.environ.get("CENTRAL_TLS_KEY", "/run/tls/key.pem")

if os.path.isfile(CERT) and os.path.isfile(KEY):
    certfile = CERT
    keyfile = KEY
    print(f"[TLS] HTTPS enabled ({CERT})")
