#!/usr/bin/env bash
# =====================================================================
# make-cert.sh - สร้างใบรับรอง HTTPS แบบ self-signed ให้หน้าเว็บ (keys/tls/cert.pem + key.pem)
# =====================================================================
#   ./scripts/make-cert.sh                    ใช้ชื่อเครื่อง + IP ของเครื่องนี้
#   ./scripts/make-cert.sh 10.0.0.5 monitor.local   ระบุ IP / ชื่อที่จะใช้เปิดเว็บ (หลายค่าได้)
#
# หลังสร้าง: cd docker && docker compose up -d --force-recreate central-api
#   แล้วเปิด https://<IP>:8090  (browser จะเตือนเพราะ self-signed -> กดยอมรับครั้งเดียว)
#   มีใบรับรองจริงของบริษัท: วาง cert.pem (รวม chain) + key.pem ใน keys/tls/ แทนการรันสคริปต์นี้
# ปิด HTTPS: ลบ keys/tls/cert.pem แล้ว recreate central-api เหมือนเดิม
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
command -v openssl >/dev/null || { echo "ต้องมี openssl (sudo apt install -y openssl)" >&2; exit 1; }

NAMES=("$@")
if [ ${#NAMES[@]} -eq 0 ]; then
  NAMES=("$(hostname)" localhost 127.0.0.1)
  for ip in $(hostname -I 2>/dev/null); do NAMES+=("$ip"); done
fi

SAN=""
for n in "${NAMES[@]}"; do
  if [[ "$n" =~ ^[0-9.]+$ || "$n" == *:* ]]; then SAN+="IP:$n,"; else SAN+="DNS:$n,"; fi
done
SAN="${SAN%,}"

mkdir -p keys/tls
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 825 \
  -keyout keys/tls/key.pem -out keys/tls/cert.pem \
  -subj "/CN=${NAMES[0]}/O=central-monitor" -addext "subjectAltName=$SAN" 2>/dev/null
chmod 600 keys/tls/key.pem
chmod 644 keys/tls/cert.pem
echo "สร้าง keys/tls/cert.pem + key.pem แล้ว (ใช้ได้ 825 วัน) สำหรับ: $SAN"
echo "ต่อไป: cd docker && docker compose up -d --force-recreate central-api   แล้วเปิด https://<IP>:8090"
