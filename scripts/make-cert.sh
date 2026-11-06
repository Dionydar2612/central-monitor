#!/usr/bin/env bash
# =====================================================================
# make-cert.sh - สร้างใบรับรอง HTTPS แบบ self-signed ให้หน้าเว็บ (keys/tls/cert.pem + key.pem)
# =====================================================================
#   ./scripts/make-cert.sh                    ใช้ชื่อเครื่อง + IP ของเครื่องนี้
#   ./scripts/make-cert.sh 10.0.0.5 monitor.local   ระบุ IP / ชื่อที่จะใช้เปิดเว็บ (หลายค่าได้)
#   ./scripts/make-cert.sh agent [IP/ชื่อ ...]       ใบรับรองของ central-agent-gw (:4318) -> keys/agent-tls/
#        ใส่ทุก IP/ชื่อที่เครื่อง Windows ใช้เรียก central แล้วรัน cm-update + ส่ง central-ca.pem ใหม่ให้ agent
#
# หลังสร้าง: cd docker && docker compose up -d --force-recreate central-api
#   แล้วเปิด https://<IP>:8090  (browser จะเตือนเพราะ self-signed -> กดยอมรับครั้งเดียว)
#   มีใบรับรองจริงของบริษัท: วาง cert.pem (รวม chain) + key.pem ใน keys/tls/ แทนการรันสคริปต์นี้
# ปิด HTTPS: ลบ keys/tls/cert.pem แล้ว recreate central-api เหมือนเดิม
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
command -v openssl >/dev/null || { echo "ต้องมี openssl (sudo apt install -y openssl)" >&2; exit 1; }

DIR=keys/tls
if [ "${1:-}" = "agent" ]; then DIR=keys/agent-tls; shift; fi
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

mkdir -p "$DIR"
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 825 \
  -keyout "$DIR/key.pem" -out "$DIR/cert.pem" \
  -subj "/CN=${NAMES[0]}/O=central-monitor" -addext "subjectAltName=$SAN" 2>/dev/null
chmod 600 "$DIR/key.pem"
chmod 644 "$DIR/cert.pem"
echo "สร้าง $DIR/cert.pem + key.pem แล้ว (ใช้ได้ 825 วัน) สำหรับ: $SAN"
if [ "$DIR" = keys/agent-tls ]; then
  echo "ต่อไป: cd docker && docker compose up -d --force-recreate buffer-init central-agent-gw"
  echo "      แล้วดาวน์โหลดชุดติดตั้งใหม่จากหน้า Settings (มี central-ca.pem ตัวใหม่) ให้เครื่อง Windows"
  exit 0
fi
echo "ต่อไป: cd docker && docker compose up -d --force-recreate central-api   แล้วเปิด https://<IP>:8090"
