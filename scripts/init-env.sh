#!/usr/bin/env bash
# =====================================================================
# init-env.sh - สร้างไฟล์ docker/.env (ไม่ถามอะไร)
# =====================================================================
# ใช้เมื่อ : ติดตั้งครั้งแรก (install.sh เรียกให้อัตโนมัติ)
# ทำอะไร   : copy docker/.env.example -> docker/.env แล้วแทนค่า CHANGE_ME ด้วย
#            - login หน้าเว็บ  = admin / admin   (ค่าเริ่มต้น เปลี่ยนทีหลังได้)
#            - SECRET_KEY สำหรับ session (สุ่ม 64 ตัวอักษร)
#            - รหัสผ่าน OpenSearch (สุ่ม ผ่านกฎความซับซ้อน ใช้ค่าเดียวกัน 2 ตัวแปร)
#            - HOST_UID / HOST_GID (uid/gid ของผู้รันสคริปต์ ใช้เป็น user ของ container)
# อยากตั้ง login เองตั้งแต่ต้น:
#            UI_USER=somchai UI_PASS='รหัสของคุณ' ./scripts/init-env.sh
# ปลอดภัย  : ถ้า docker/.env มีอยู่แล้วจะไม่เขียนทับ (ออกด้วย error)
#            ไฟล์ที่สร้างมีสิทธิ์ 600 (อ่านได้เฉพาะเจ้าของ)
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."      # ย้ายไปโฟลเดอร์รากของโปรเจกต์ ไม่ว่าจะเรียกจากที่ไหน

ENV_FILE="docker/.env"

if [ -e "$ENV_FILE" ]; then
  echo "$ENV_FILE already exists - not touching it." >&2
  exit 1
fi

command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

UI_USER="${UI_USER:-admin}"
UI_PASS="${UI_PASS:-admin}"
SECRET="$(openssl rand -hex 32)"
OS_PASS="${OS_PASS:-$(openssl rand -hex 12)Aa1@}"   # meets OpenSearch complexity rules (install.sh ส่งรหัสเดิมมาเมื่อมี volume เดิม)
H_UID="${HOST_UID:-$(id -u)}"; H_GID="${HOST_GID:-$(id -g)}"   # uid/gid ที่ container ใช้ = เจ้าของไฟล์บน host

# escape ตัวที่ sed ตีความในส่วนแทนที่ (\\ & |) ไม่งั้นรหัสผ่านอย่าง pa&ss|word จะพังไฟล์ .env
esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }
UI_USER_E="$(esc "$UI_USER")"; UI_PASS_E="$(esc "$UI_PASS")"; OS_PASS_E="$(esc "$OS_PASS")"

umask 077
sed \
  -e "s|^CENTRAL_AUTH_USERNAME=.*|CENTRAL_AUTH_USERNAME=${UI_USER_E}|" \
  -e "s|^CENTRAL_AUTH_PASSWORD=.*|CENTRAL_AUTH_PASSWORD=${UI_PASS_E}|" \
  -e "s|^CENTRAL_SECRET_KEY=.*|CENTRAL_SECRET_KEY=${SECRET}|" \
  -e "s|^OPENSEARCH_ADMIN_PASSWORD=.*|OPENSEARCH_ADMIN_PASSWORD=${OS_PASS_E}|" \
  -e "s|^OPENSEARCH_INITIAL_ADMIN_PASSWORD=.*|OPENSEARCH_INITIAL_ADMIN_PASSWORD=${OS_PASS_E}|" \
  -e "s|^HOST_UID=.*|HOST_UID=${H_UID}|" \
  -e "s|^HOST_GID=.*|HOST_GID=${H_GID}|" \
  docker/.env.example > "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "Created $ENV_FILE (mode 600)."
echo "Web UI login  ->  user: ${UI_USER}   password: ${UI_PASS}"
if [ "$UI_PASS" = "admin" ]; then
  echo "(default admin/admin - change later: edit CENTRAL_AUTH_* in $ENV_FILE, see docs/CONFIG-TH.md)"
fi
